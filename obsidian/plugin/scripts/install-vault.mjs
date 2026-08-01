import { copyFile, mkdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifacts = ["main.js", "manifest.json", "styles.css"];

async function requireDirectory(directory, message) {
  try {
    if ((await stat(directory)).isDirectory()) return;
  } catch {}
  throw new Error(message);
}

async function requireFile(file) {
  try {
    if ((await stat(file)).isFile()) return;
  } catch {}
  throw new Error(`Required build artifact is missing: ${path.basename(file)}`);
}

export async function installVault(vault) {
  if (!path.isAbsolute(vault)) {
    throw new Error("Vault path must be absolute");
  }

  const obsidianDirectory = path.join(vault, ".obsidian");
  await requireDirectory(
    obsidianDirectory,
    "Vault must contain an .obsidian directory",
  );

  const sourceFiles = artifacts.map((artifact) => path.join(pluginRoot, artifact));
  await Promise.all(sourceFiles.map(requireFile));

  const destination = path.join(obsidianDirectory, "plugins", "yield-wiki");
  await mkdir(destination, { recursive: true });
  await Promise.all(
    artifacts.map((artifact) =>
      copyFile(path.join(pluginRoot, artifact), path.join(destination, artifact)),
    ),
  );
}

function vaultArgument(argv) {
  const index = argv.indexOf("--vault");
  if (index === -1 || !argv[index + 1]) {
    throw new Error("Usage: npm run install:vault -- --vault <absolute-vault-path>");
  }
  return argv[index + 1];
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const vault = vaultArgument(process.argv.slice(2));
    await installVault(vault);
    process.stdout.write("Yield Wiki plugin installed.\n");
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
