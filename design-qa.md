# Design QA

- Source target: `design/reference-library-workbench.png` (user-selected option 1)
- Implementation screenshot: `design/qa/library-dual-source.png`
- Settings alignment screenshot: `design/qa/settings-alignment.png`
- Chrome side-panel screenshot: `design/qa/sidepanel-monochrome.png`
- Viewport: 1440 x 1024, Chrome headless; additionally inspected in the in-app browser

## Comparison history

1. Initial workspace used several accent colors and a card-heavy dashboard. It
   did not match the requested Zotero-like density or monochrome hierarchy.
2. The selected direction replaced that shell with a compact three-pane table,
   thin neutral dividers, restrained grayscale states, and an inspector for
   original, translation, guide, and notes.
3. Settings QA found the page title flush against the sidebar divider and a
   mismatch between sidebar-brand and toolbar baselines. Non-library views now
   have 30 px horizontal padding, and both header regions use a 58 px height.
4. Library-source QA separated automatic ScholarSplit intake from synced Zotero
   collections and added an explicit Zotero-folder destination action without
   introducing a second accent color.
5. The Chrome side panel now uses the same monochrome canvas, 58 px header,
   square cards, thin dividers, compact type scale, and solid black primary
   action as the workspace. The previous navy, gold, red, patterned background,
   and decorative shadows were removed.

## Final result

passed
