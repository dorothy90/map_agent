import esbuild from "esbuild";

const production = process.argv[2] === "production";

await esbuild.build({
  entryPoints: ["src/main.ts"],
  bundle: true,
  external: ["obsidian"],
  format: "cjs",
  platform: "node",
  target: "es2022",
  outfile: "main.js",
  minify: production,
  sourcemap: production ? false : "inline",
  logLevel: "info",
});
