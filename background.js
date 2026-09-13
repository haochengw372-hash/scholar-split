async function configureActionSidePanel() {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false });
}

chrome.runtime.onInstalled.addListener(() => {
  void configureActionSidePanel();
});

chrome.runtime.onStartup.addListener(() => {
  void configureActionSidePanel();
});

chrome.action.onClicked.addListener((tab) => {
  if (!Number.isInteger(tab.id) || !Number.isInteger(tab.windowId)) return;
  // This must be the first Chrome API call in the synchronous click handler.
  // Chrome consumes the user gesture if any asynchronous API runs first.
  void chrome.sidePanel.open({ windowId: tab.windowId }).catch((error) => {
    console.error("Unable to open ScholarSplit side panel", error);
  });
  void chrome.storage.session.set({
    invokedTab: {
      id: tab.id,
      windowId: tab.windowId,
      url: tab.url || tab.pendingUrl || "",
      title: tab.title || ""
    }
  }).catch((error) => {
    console.error("Unable to remember the invoked tab", error);
  });
});

void configureActionSidePanel();
