import { ScholarSplitZoteroBridge } from "./scholarSplitBridge";
import { getPref } from "../utils/prefs";

export class ScholarSplitSyncFactory {
    private static bridge: ScholarSplitZoteroBridge | null = null;

    static async start(): Promise<void> {
        if (this.bridge) return;
        try {
            const homeDir = Services.dirsvc.get("Home", Ci.nsIFile).path;
            let baseUrl = String(getPref("new_serverip")).replace(/\/$/, "");
            if (new URL(baseUrl).hostname === "localhost") {
                baseUrl = baseUrl.replace("localhost", "127.0.0.1");
            }
            if (
                !["127.0.0.1", "localhost", "[::1]"].includes(
                    new URL(baseUrl).hostname,
                )
            ) {
                Zotero.debug(
                    "[ScholarSplit] Pairing is restricted to the local service",
                );
                return;
            }
            const configuredPath = String(
                getPref("scholarSplitTokenPath") || "",
            );
            const candidates = configuredPath
                ? [configuredPath]
                : [
                      PathUtils.join(
                          homeDir,
                          "Library",
                          "Application Support",
                          "ScholarSplit",
                          "server",
                          "data",
                          "zotero-pairing-token",
                      ),
                      PathUtils.join(
                          homeDir,
                          ".local",
                          "share",
                          "ScholarSplit",
                          "server",
                          "data",
                          "zotero-pairing-token",
                      ),
                      PathUtils.join(
                          homeDir,
                          "zotero-pdf2zh",
                          "server",
                          "data",
                          "zotero-pairing-token",
                      ),
                  ];
            let token = "";
            for (const tokenPath of candidates) {
                if (!(await IOUtils.exists(tokenPath))) continue;
                const candidate = (await IOUtils.readUTF8(tokenPath)).trim();
                const response = await Zotero.HTTP.request(
                    "GET",
                    `${baseUrl}/api/v1/zotero/commands?bridgeId=zotero-desktop-main`,
                    {
                        headers: { Authorization: `Bearer ${candidate}` },
                        successCodes: [200, 403],
                        timeout: 3000,
                    },
                );
                if (response.status === 200) {
                    token = candidate;
                    break;
                }
            }
            if (!token) {
                Zotero.debug(
                    "[ScholarSplit] No pairing token matches the running service",
                );
                return;
            }
            this.bridge = new ScholarSplitZoteroBridge({
                bridgeId: "zotero-desktop-main",
                libraryIDs: [Zotero.Libraries.userLibraryID],
                baseUrl: `${baseUrl}/api/v1/`,
                pairingToken: token,
                pollIntervalMs: 2000,
                logger: {
                    debug: (message) =>
                        Zotero.debug(`[ScholarSplit] ${message}`),
                    info: (message) =>
                        Zotero.debug(`[ScholarSplit] ${message}`),
                    warn: (message) =>
                        Zotero.debug(`[ScholarSplit] ${message}`),
                    error: (message, context) =>
                        Zotero.debug(
                            `[ScholarSplit] ${message}: ${String(context?.error || "unknown")}`,
                        ),
                },
            });
            await this.bridge.start();
            Zotero.debug("[ScholarSplit] Zotero sync started");
        } catch (error) {
            this.bridge = null;
            Zotero.debug(
                `[ScholarSplit] Zotero sync unavailable: ${String(error)}`,
            );
        }
    }

    static stop(): void {
        this.bridge?.stop();
        this.bridge = null;
    }
}
