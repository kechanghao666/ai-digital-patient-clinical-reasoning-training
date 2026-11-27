// Build the static front end into web/dist (served by FastAPI). Usage: node build.mjs [--watch]
import { build, context } from "esbuild";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/assets", { recursive: true });
copyFileSync("index.html", "dist/index.html");
const runtimePackages = ["react", "react-dom", "scheduler"];
const notices = runtimePackages.map((name) => `${name}\n${readFileSync(`node_modules/${name}/LICENSE`, "utf8")}`);
writeFileSync("dist/assets/third-party-licenses.txt", notices.join("\n"));
const options = {
  entryPoints: { main: "src/main.tsx" }, bundle: true, outdir: "dist/assets", format: "esm",
  minify: !watch, sourcemap: watch, target: ["chrome110", "edge110", "firefox110", "safari16"],
  jsx: "automatic", define: { "process.env.NODE_ENV": watch ? '"development"' : '"production"' }, logLevel: "info",
  legalComments: "linked",
};
if (watch) { const ctx = await context(options); await ctx.watch(); } else { await build(options); }
