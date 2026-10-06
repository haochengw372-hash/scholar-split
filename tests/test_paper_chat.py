import base64
import json
import stat

import fitz
from flask import Flask
import pytest

from integrations.server.paper_chat import PaperChatService, InvalidChatResponse, select_context, validate_answer
from integrations.server.workspace_api import create_workspace_blueprint


def pdf_data(text="The sample consisted of 30 participants from one university."):
    pdf = fitz.open()
    page = pdf.new_page()
    if text:
        page.insert_text((72, 72), text)
    result = base64.b64encode(pdf.tobytes()).decode()
    pdf.close()
    return result


def answer(quote="The sample consisted of 30 participants from one university."):
    return {"answer": "样本来自一所大学，共30人。[1]", "citations": [{"page": 1, "quote": quote}]}


def test_document_identity_conversation_isolation_restart_and_clear(tmp_path):
    prompts = []
    def provider(prompt):
        prompts.append(prompt)
        return answer(), "test-model"
    chat = PaperChatService(tmp_path, provider)
    original = pdf_data()
    first = chat.register(original, "one.pdf")["documentId"]
    assert chat.register(original, "renamed.pdf")["documentId"] == first
    second = chat.register(pdf_data("A second independent article."), "two.pdf")["documentId"]
    result = chat.ask(first, "样本是什么？")
    assert result["citations"][0]["page"] == 1
    assert result["coverage"] == "full_text"
    restored = PaperChatService(tmp_path, provider)
    assert len(restored.history(first)["messages"]) == 2
    assert restored.history(second)["messages"] == []
    restored.ask(first, "他们来自哪里？")
    assert '"history": [{"id"' in prompts[-1]
    assert len(restored.history(first)["messages"]) == 4
    restored.clear(first)
    assert restored.history(first)["messages"] == []
    assert stat.S_IMODE(chat.path.stat().st_mode) == 0o600


def test_citation_retry_is_bounded_and_failed_answers_do_not_enter_history(tmp_path):
    calls = []
    def provider(prompt):
        calls.append(prompt)
        return answer("This sentence is not in the PDF."), "test-model"
    chat = PaperChatService(tmp_path, provider)
    document = chat.register(pdf_data(), "paper.pdf")["documentId"]
    with pytest.raises(InvalidChatResponse, match="未在"):
        chat.ask(document, "样本是什么？")
    assert len(calls) == 2
    assert chat.history(document)["messages"] == []


def test_format_retry_can_recover_but_provider_errors_are_not_retried(tmp_path):
    responses = iter([{"answer": "缺少引用结构"}, answer()])
    chat = PaperChatService(tmp_path, lambda prompt: (next(responses), "model"))
    document = chat.register(pdf_data(), "paper.pdf")["documentId"]
    assert chat.ask(document, "样本？")["answer"].endswith("[1]")
    calls = []
    def failed(prompt):
        calls.append(prompt)
        raise ValueError("研究模型请求失败（HTTP 402）")
    chat.request_json = failed
    with pytest.raises(ValueError, match="402"):
        chat.ask(document, "再问一个问题")
    assert len(calls) == 1
    assert len(chat.history(document)["messages"]) == 2


def test_only_supplied_pages_and_exact_quotes_can_be_cited():
    context = [{"page": 2, "text": "A sufficiently precise passage with real evidence."}]
    good = {"answer": "证据在这里。[1]", "citations": [{"page": 2, "quote": "real evidence"}]}
    assert validate_answer(good, context)["citations"][0]["page"] == 2
    with pytest.raises(InvalidChatResponse):
        validate_answer({**good, "citations": [{"page": 1, "quote": "real evidence"}]}, context)
    with pytest.raises(InvalidChatResponse):
        validate_answer({**good, "answer": "证据。[9]"}, context)
    assert validate_answer({"answer": "论文没有回答这个问题。", "citations": []}, context)["citations"] == []


def test_long_paper_retrieval_includes_relevant_and_requested_pages_within_budget():
    pages = [{"page": i, "text": ("ordinary background discussion " * 120)} for i in range(1, 61)]
    pages[48]["text"] = "Unique causal independence mechanism. " * 120
    context = select_context(pages, ["causal independence"], {55})
    assert {1, 49, 55} <= {p["page"] for p in context}
    assert sum(len(p["text"]) for p in context) <= 100_000


def test_scan_without_text_and_empty_question_are_explicit_errors(tmp_path):
    chat = PaperChatService(tmp_path, lambda prompt: (answer(), "model"))
    with pytest.raises(ValueError, match="OCR"):
        chat.register(pdf_data(""), "scanned.pdf")
    document = chat.register(pdf_data(), "paper.pdf")["documentId"]
    with pytest.raises(ValueError, match="请输入"):
        chat.ask(document, " ")


def test_long_document_keyword_json_retry_and_coverage_prompt(tmp_path):
    pdf = fitz.open()
    quote = "Ordinary background discussion without the target keyword."
    for _ in range(60):
        page = pdf.new_page()
        for line in range(35):
            page.insert_text((72, 45 + line * 20), quote, fontsize=7)
    encoded = base64.b64encode(pdf.tobytes()).decode()
    pdf.close()
    prompts = []
    def provider(prompt):
        prompts.append(prompt)
        if "Translate the current question" in prompt:
            return (None if len(prompts) == 1 else {"terms": ["background"]}), "model"
        assert '"coverage": "retrieved_passages"' in prompt
        assert "not that the whole paper lacks it" in prompt
        return {"answer": "检索片段说明背景。[1]", "citations": [{"page": 1, "quote": quote}]}, "model"
    chat = PaperChatService(tmp_path, provider)
    document = chat.register(encoded, "long.pdf")["documentId"]
    assert chat.ask(document, "这个背景是什么？")["coverage"] == "retrieved_passages"
    assert len(prompts) == 3


def test_api_local_boundary_history_missing_document_and_conversation(tmp_path):
    chat = PaperChatService(tmp_path, lambda prompt: (answer(), "model"))
    app = Flask(__name__)
    app.register_blueprint(create_workspace_blueprint(None, tmp_path, tmp_path, tmp_path, chat_service=chat))
    client = app.test_client()
    route = "/api/v1/paper-chat/documents"
    assert client.post(route, json={"fileContent": pdf_data()}, headers={"Origin": "https://external.example"}).status_code == 403
    assert client.post(route, json={"fileContent": pdf_data()}, environ_base={"REMOTE_ADDR": "10.0.0.1"}).status_code == 403
    assert client.get(route + "/missing/messages").status_code == 404
    accepted = client.post(route, json={"fileContent": pdf_data(), "fileName": "paper.pdf"})
    assert accepted.status_code == 201
    document = accepted.get_json()["data"]["documentId"]
    assert client.post(route + f"/{document}/ask", json={"question": "样本？"}).status_code == 200
    assert len(client.get(route + f"/{document}/messages").get_json()["data"]["messages"]) == 2
    assert client.delete(route + f"/{document}/messages").get_json()["data"]["messages"] == []
