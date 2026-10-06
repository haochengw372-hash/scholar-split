import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

/** Exercise the real file helper with the browser FileReader data-URL contract. */
export function pdfHelper(bytes: Uint8Array, readError?: Error) {
    class FileReader {
        result = "";
        onload?: () => void;
        onloadend?: () => void;
        onerror?: (error: unknown) => void;
        readAsDataURL(blob: Blob) {
            if (readError) {
                this.onerror?.(readError);
                this.onloadend?.();
                return;
            }
            void blob.arrayBuffer().then(
                (buffer) => {
                    this.result = `data:${blob.type};base64,${Buffer.from(buffer).toString("base64")}`;
                    this.onload?.();
                    this.onloadend?.();
                },
                (error) => this.onerror?.(error),
            );
        }
    }
    const dependencies: Record<string, unknown> = {
        "../utils/prefs": {},
        "../utils/locale": {},
        "../../package.json": { version: "test" },
        "./pdf2zhFileProcessor": {},
        "./pdf2zhAttachmentUtils": {},
        "./pdf2zhTypes": {},
        "./preferenceScript": {},
        "./attachmentIdentity": {},
    };
    const module = { exports: {} };
    const source = readFileSync(
        new URL("../src/modules/pdf2zhHelper.ts", import.meta.url),
        "utf8",
    );
    vm.runInNewContext(
        ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText,
        {
            module,
            exports: module.exports,
            require: (name: string) => dependencies[name],
            Blob,
            FileReader,
            IOUtils: { read: async () => bytes, exists: async () => true },
            PathUtils: { filename: (path: string) => path.split("/").at(-1) },
        },
    );
    return (module.exports as any).PDF2zhHelperFactory;
}

export const pdfAttachment = {
    isAttachment: () => true,
    getFilePath: () => "/tmp/current bilingual.pdf",
};
