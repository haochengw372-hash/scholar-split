import { defineConfig } from "zotero-plugin-scaffold";
import pkg from "./package.json";

export default defineConfig({
    source: ["src", "addon"],
    dist: "build",
    name: pkg.config.addonName,
    id: pkg.config.addonID,
    namespace: pkg.config.addonRef,
    // Preserve the addon ID to update existing guide installations, but never
    // replace this fork with an upstream XPI lacking ScholarSplit features.
    updateURL: "https://raw.githubusercontent.com/haochengw372-hash/scholar-split/main/integrations/zotero/update.json",
    xpiDownloadLink:
        "https://github.com/{{owner}}/{{repo}}/releases/download/v{{version}}/{{xpiName}}.xpi",

    build: {
        assets: ["addon/**/*.*"],
        define: {
            ...pkg.config,
            author: pkg.author,
            description: pkg.description,
            homepage: pkg.homepage,
            buildVersion: pkg.version,
            buildTime: "{{buildTime}}",
        },
        esbuildOptions: [
            {
                entryPoints: ["src/index.ts"],
                define: {
                    __env__: `"${process.env.NODE_ENV}"`,
                },
                bundle: true,
                target: "firefox115",
                outfile: `build/addon/content/scripts/${pkg.config.addonRef}.js`,
            },
        ],
    },

    // If you need to see a more detailed log, uncomment the following line:
    // logLevel: "trace",
});
