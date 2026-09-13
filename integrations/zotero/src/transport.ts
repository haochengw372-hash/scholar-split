import {
  BridgeTransport,
  CommandAck,
  CommandBatch,
  SnapshotEnvelope,
} from "./types";
import {
  assertCommandBatch,
  assertLoopbackBaseUrl,
  createAuthorizationHeaders,
} from "./protocol";

/** HTTP transport for the loopback companion. Pairing token never enters logs. */
export class LoopbackTransport implements BridgeTransport {
  private readonly baseUrl: URL;
  private readonly headers: HeadersInit;
  private readonly bridgeId: string;

  constructor(baseUrl: string, pairingToken: string, bridgeId: string) {
    this.baseUrl = assertLoopbackBaseUrl(baseUrl);
    this.headers = createAuthorizationHeaders(pairingToken);
    this.bridgeId = bridgeId;
  }

  async sendSnapshot(snapshot: SnapshotEnvelope): Promise<void> {
    await this.request("zotero/snapshots", {
      method: "POST",
      body: JSON.stringify(snapshot),
    });
  }

  async pollCommands(afterCursor: string): Promise<CommandBatch> {
    const params = new URLSearchParams({
      bridgeId: this.bridgeId,
      after: afterCursor,
    });
    const response = await this.request(`zotero/commands?${params.toString()}`, {
      method: "GET",
    });
    const envelope: unknown = await response.json();
    const payload: unknown =
      envelope && typeof envelope === "object" && "data" in envelope
        ? (envelope as { data: unknown }).data
        : envelope;
    assertCommandBatch(payload);
    return payload;
  }

  async acknowledge(ack: CommandAck): Promise<void> {
    await this.request("zotero/acks", {
      method: "POST",
      body: JSON.stringify(ack),
    });
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    const endpoint = new URL(path, this.baseUrl);
    const response = await fetch(endpoint, { ...init, headers: this.headers });
    if (!response.ok) {
      // Do not include response bodies: they may reflect request credentials.
      throw new Error(`Loopback bridge request failed with HTTP ${response.status}`);
    }
    return response;
  }
}
