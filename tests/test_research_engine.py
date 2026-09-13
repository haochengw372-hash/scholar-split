import json
import unittest

from integrations.server.research_engine import (
    ACQUISITION_STATUSES,
    EVIDENCE_LABELS,
    AcquisitionTransitionError,
    ResearchValidationError,
    build_exploratory_prompt,
    build_fts_query,
    build_systematic_prompt,
    corpus_hash,
    dedupe_recommendations,
    make_cache_key,
    normalize_arxiv_id,
    normalize_doi,
    normalize_fts_terms,
    rank_recommendations,
    recommendation_identity,
    score_recommendation,
    validate_acquisition_transition,
    validate_and_normalize_evidence_matrix,
    validate_gap_json,
    validate_synthesis_json,
)


class EvidenceMatrixTests(unittest.TestCase):
    def test_normalizes_aliases_metadata_and_duplicate_rows(self):
        payload = {
            "rows": [
                {
                    "studyId": "  S-1 ",
                    "statement": " AI   disclosure changes trust ",
                    "quote": " Participants reported less trust. ",
                    "page": 12,
                    "DOI": "ignored input key",
                    "doi": "https://doi.org/10.1234/Example.1",
                    "arxivId": "arXiv:2401.01234v2",
                    "sourceTitle": "  A Study  ",
                    "year": "2024",
                },
                {
                    "source_id": "S-1",
                    "claim": "AI disclosure changes trust",
                    "evidence": "Participants reported less trust.",
                    "location": "12",
                },
            ]
        }

        rows = validate_and_normalize_evidence_matrix(payload)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], "S-1")
        self.assertEqual(rows[0]["claim"], "AI disclosure changes trust")
        self.assertEqual(rows[0]["location"], "12")
        self.assertEqual(rows[0]["doi"], "10.1234/example.1")
        self.assertEqual(rows[0]["arxiv_id"], "2401.01234")
        self.assertEqual(rows[0]["title"], "A Study")
        self.assertEqual(rows[0]["year"], 2024)
        self.assertEqual(rows[0]["label"], "evidence")

    def test_accepts_bare_list_and_preserves_valid_label(self):
        rows = validate_and_normalize_evidence_matrix(
            [{"source": "s", "finding": "c", "excerpt": "e", "label": "Inference"}]
        )
        self.assertEqual(rows[0]["label"], "inference")

    def test_rejects_non_rows_missing_fields_bad_label_and_bad_year(self):
        invalid = [
            None,
            [{"source_id": "s", "claim": "c"}],
            [{"source_id": "s", "claim": "c", "evidence": "e", "label": "fact"}],
            [{"source_id": "s", "claim": "c", "evidence": "e", "year": "recent"}],
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ResearchValidationError):
                    validate_and_normalize_evidence_matrix(payload)

    def test_rejects_secret_fields_at_any_depth(self):
        with self.assertRaisesRegex(ResearchValidationError, "must not be included"):
            validate_and_normalize_evidence_matrix(
                [
                    {
                        "source_id": "s",
                        "claim": "c",
                        "evidence": "e",
                        "metadata": {"openai_api_key": "sk-not-retained"},
                    }
                ]
            )


class LabeledJsonTests(unittest.TestCase):
    def test_synthesis_parses_json_and_normalizes_claims(self):
        result = validate_synthesis_json(
            json.dumps(
                {
                    "claims": [
                        {
                            "claim": " Supported result ",
                            "label": "Evidence",
                            "sourceIds": [" S1 ", "S1", "S2"],
                        },
                        {"statement": "Needs a test", "label": "hypothesis"},
                    ]
                }
            )
        )
        self.assertEqual(result["schema_version"], "1")
        self.assertEqual(result["claims"][0]["text"], "Supported result")
        self.assertEqual(result["claims"][0]["evidence_refs"], ["S1", "S2"])
        self.assertEqual(result["claims"][1]["evidence_refs"], [])

    def test_evidence_label_requires_reference(self):
        with self.assertRaisesRegex(ResearchValidationError, "requires evidence_refs"):
            validate_synthesis_json(
                {"claims": [{"text": "unsupported", "label": "evidence"}]}
            )

    def test_every_label_is_accepted_and_unknown_label_is_rejected(self):
        claims = []
        for label in sorted(EVIDENCE_LABELS):
            claims.append(
                {
                    "text": label,
                    "label": label,
                    "evidence_refs": ["S1"] if label == "evidence" else [],
                }
            )
        self.assertEqual(len(validate_synthesis_json({"claims": claims})["claims"]), 4)
        with self.assertRaises(ResearchValidationError):
            validate_synthesis_json({"claims": [{"text": "x", "label": "opinion"}]})

    def test_gap_json_uses_same_provenance_contract(self):
        result = validate_gap_json(
            {"gaps": [{"gap": "No longitudinal study", "label": "unverified"}]}
        )
        self.assertEqual(result["gaps"][0], {
            "text": "No longitudinal study",
            "label": "unverified",
            "evidence_refs": [],
        })

    def test_rejects_invalid_json_or_collection_shape(self):
        for payload in ("{bad", {}, {"claims": {}}, {"claims": ["not an object"]}):
            with self.subTest(payload=payload):
                with self.assertRaises(ResearchValidationError):
                    validate_synthesis_json(payload)


class PromptTests(unittest.TestCase):
    MATRIX = [{"source_id": "S1", "claim": "A", "evidence": "Quote"}]

    def test_exploratory_prompt_marks_scope_labels_and_json_contract(self):
        prompt = build_exploratory_prompt(
            "How does AI disclosure affect trust?",
            self.MATRIX,
            context={"languages": ["en", "zh"]},
            max_items=7,
        )
        self.assertIn("exploratory, non-exhaustive", prompt)
        self.assertIn("at most 7 claims", prompt)
        self.assertIn("evidence_refs", prompt)
        for label in EVIDENCE_LABELS:
            self.assertIn(label, prompt)

    def test_systematic_prompt_contains_audit_boundaries(self):
        prompt = build_systematic_prompt(
            "RQ",
            self.MATRIX,
            protocol={"databases": ["OpenAlex"]},
            inclusion_criteria=["peer reviewed"],
            exclusion_criteria="no abstract",
        )
        self.assertIn("systematic, protocol-bounded", prompt)
        self.assertIn("negative findings", prompt)
        self.assertIn("included_source_ids", prompt)
        self.assertIn("peer reviewed", prompt)

    def test_prompt_builder_rejects_secret_bearing_context(self):
        with self.assertRaises(ResearchValidationError):
            build_exploratory_prompt("RQ", context={"api-key": "do-not-copy"})


class HashAndFtsTests(unittest.TestCase):
    def test_corpus_hash_is_stable_across_record_and_key_order(self):
        first = [{"id": "b", "year": 2022}, {"year": 2021, "id": "a"}]
        second = [{"id": "a", "year": 2021}, {"id": "b", "year": 2022}]
        self.assertEqual(corpus_hash(first), corpus_hash(second))
        self.assertEqual(len(corpus_hash(first)), 64)

    def test_corpus_hash_changes_with_content(self):
        self.assertNotEqual(corpus_hash([{"id": "a"}]), corpus_hash([{"id": "b"}]))

    def test_cache_key_is_deterministic_and_scoped(self):
        corpus = [{"id": "a"}]
        first = make_cache_key("synthesis", corpus, {"language": "zh"})
        second = make_cache_key("synthesis", corpus, {"language": "zh"})
        self.assertEqual(first, second)
        self.assertNotEqual(first, make_cache_key("gaps", corpus, {"language": "zh"}))
        self.assertNotEqual(first, make_cache_key("synthesis", corpus, {"language": "en"}))

    def test_hash_and_cache_key_reject_secrets(self):
        with self.assertRaises(ResearchValidationError):
            corpus_hash({"documents": [], "access_token": "secret"})
        with self.assertRaises(ResearchValidationError):
            make_cache_key("x", [], {"clientSecret": "secret"})

    def test_fts_query_strips_operators_and_hostile_syntax(self):
        self.assertEqual(
            normalize_fts_terms('AI OR "trust" NOT title:* -- methods'),
            ["ai", "trust", "title", "methods"],
        )
        self.assertEqual(
            build_fts_query('AI OR "trust" NOT title:* -- methods'),
            '"ai"* AND "trust"* AND "title"* AND "methods"*',
        )

    def test_fts_query_supports_or_exact_column_and_empty(self):
        self.assertEqual(
            build_fts_query("trust disclosure", operator="or", prefix=False, column="abstract"),
            'abstract : ("trust" OR "disclosure")',
        )
        self.assertEqual(build_fts_query(" -- OR NOT "), "")
        with self.assertRaises(ResearchValidationError):
            build_fts_query("x", column="title); DROP TABLE papers;--")


class RecommendationTests(unittest.TestCase):
    def test_identifier_normalization(self):
        self.assertEqual(normalize_doi(" DOI: 10.1000/ABC.1. "), "10.1000/abc.1")
        self.assertEqual(normalize_doi("not-a-doi"), "")
        self.assertEqual(normalize_arxiv_id("https://arxiv.org/pdf/2401.12345v3.pdf"), "2401.12345")
        self.assertEqual(
            recommendation_identity({"title": "A: Résumé!", "year": "2022"}),
            ("title_year", "aresume:2022"),
        )

    def test_dedupe_matches_doi_arxiv_and_title_year(self):
        items = [
            {"title": "Paper A", "year": 2024, "doi": "10.1234/A", "score": 0.2},
            {"title": "Better metadata", "year": 2024, "doi": "https://doi.org/10.1234/a", "score": 0.8},
            {"title": "Preprint", "year": 2023, "arxiv_id": "2401.12345v1"},
            {"title": "Preprint revised", "year": 2023, "arxiv_id": "arXiv:2401.12345v4"},
            {"title": "Café Effects", "year": 2020},
            {"title": "Cafe effects!", "year": "2020"},
        ]
        result = dedupe_recommendations(items)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["title"], "Better metadata")
        self.assertEqual(result[0]["duplicate_count"], 2)
        self.assertIn("doi:10.1234/a", result[0]["matched_identifiers"])

    def test_dedupe_collapses_transitive_identity_bridge(self):
        result = dedupe_recommendations(
            [
                {"title": "One", "year": 2024, "doi": "10.1234/one"},
                {"title": "Other", "year": 2024, "arxiv_id": "2401.12345"},
                {
                    "title": "One",
                    "year": 2024,
                    "doi": "10.1234/one",
                    "arxiv_id": "2401.12345",
                },
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["duplicate_count"], 3)

    def test_score_is_explainable_and_uses_all_components(self):
        result = score_recommendation(
            {
                "title": "Recent direct neighbor",
                "year": 2025,
                "local_match": 0.8,
                "citation_distance": 1,
                "gap_match": 0.6,
            },
            current_year=2025,
        )
        self.assertAlmostEqual(result["score"], 0.715)
        self.assertEqual(
            set(result["score_breakdown"]),
            {"local_match", "citation_proximity", "recency", "gap_match"},
        )
        self.assertIn("citation_proximity=0.500", result["score_explanation"])

    def test_score_derives_gap_overlap_and_recency(self):
        result = score_recommendation(
            {
                "title": "Platform governance and trust",
                "abstract": "Disclosure experiment",
                "year": 2020,
                "local_similarity": 80,
                "citation_proximity": 25,
            },
            current_year=2025,
            gap_terms=["governance", "disclosure", "missing"],
        )
        self.assertEqual(result["score_breakdown"]["local_match"]["value"], 0.8)
        self.assertEqual(result["score_breakdown"]["recency"]["value"], 0.5)
        self.assertAlmostEqual(result["score_breakdown"]["gap_match"]["value"], 2 / 3, places=6)

    def test_custom_weights_are_normalized_and_validated(self):
        result = score_recommendation(
            {"local_match": 1},
            weights={
                "local_match": 2,
                "citation_proximity": 0,
                "recency": 0,
                "gap_match": 0,
            },
        )
        self.assertEqual(result["score"], 1)
        with self.assertRaises(ResearchValidationError):
            score_recommendation({"local_match": 1}, weights={"local_match": 1})

    def test_rank_scores_before_dedupe_and_sorts_best_first(self):
        result = rank_recommendations(
            [
                {"title": "Same", "year": 2024, "doi": "10.1234/s", "local_match": 0.1},
                {"title": "Same richer", "year": 2024, "doi": "10.1234/S", "local_match": 0.9},
                {"title": "Other", "year": 2024, "local_match": 0.5},
            ],
            current_year=2024,
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Same richer")
        self.assertEqual(result[0]["duplicate_count"], 2)


class AcquisitionTransitionTests(unittest.TestCase):
    def test_status_contract_is_exact(self):
        self.assertEqual(
            ACQUISITION_STATUSES,
            {"oa", "institution", "repository", "delivery", "unavailable"},
        )

    def test_dry_run_previews_download_without_authorizing_it(self):
        result = validate_acquisition_transition(
            None, "OA", dry_run=True, download=True, batch_size=20
        )
        self.assertEqual(result["status"], "oa")
        self.assertEqual(result["action"], "preview")
        self.assertFalse(result["can_download"])
        self.assertTrue(result["requires_batch_confirmation"])

    def test_execution_requires_prior_dry_run(self):
        with self.assertRaisesRegex(AcquisitionTransitionError, "completed dry run"):
            validate_acquisition_transition(
                "oa", "repository", dry_run=False, download=True
            )

    def test_batch_execution_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(AcquisitionTransitionError, "explicit batch confirmation"):
            validate_acquisition_transition(
                "oa",
                "repository",
                dry_run=False,
                download=True,
                dry_run_completed=True,
                batch_size=2,
            )
        result = validate_acquisition_transition(
            "oa",
            "repository",
            dry_run=False,
            download=True,
            dry_run_completed=True,
            batch_size=2,
            batch_confirmed=True,
        )
        self.assertTrue(result["can_download"])

    def test_single_download_does_not_require_batch_confirmation(self):
        result = validate_acquisition_transition(
            "institution",
            "repository",
            dry_run=False,
            download=True,
            dry_run_completed=True,
        )
        self.assertTrue(result["can_download"])
        self.assertFalse(result["requires_batch_confirmation"])

    def test_unavailable_and_unknown_status_cannot_download(self):
        with self.assertRaises(AcquisitionTransitionError):
            validate_acquisition_transition(
                "repository",
                "unavailable",
                dry_run=False,
                download=True,
                dry_run_completed=True,
            )
        with self.assertRaises(AcquisitionTransitionError):
            validate_acquisition_transition(None, "pirate", dry_run=True)

    def test_dry_run_must_be_explicit_boolean(self):
        with self.assertRaises(AcquisitionTransitionError):
            validate_acquisition_transition(None, "delivery", dry_run="yes")


if __name__ == "__main__":
    unittest.main()
