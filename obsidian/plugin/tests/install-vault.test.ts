import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { installVault } from "../scripts/install-vault.mjs";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

async function makeTemporaryVault(): Promise<string> {
  const vault = await mkdtemp(path.join(tmpdir(), "yield-wiki-vault-"));
  temporaryDirectories.push(vault);
  await mkdir(path.join(vault, ".obsidian"));
  return vault;
}

describe("Vault installer", () => {
  it("installs exactly the three artifacts and preserves data.json byte-for-byte", async () => {
    const vault = await makeTemporaryVault();
    const pluginDirectory = path.join(vault, ".obsidian", "plugins", "yield-wiki");
    const savedSettings = Buffer.from('{"apiToken":"keep"}\n', "utf8");
    await mkdir(pluginDirectory, { recursive: true });
    await writeFile(path.join(pluginDirectory, "data.json"), savedSettings);

    await installVault(vault);

    expect(await readFile(path.join(pluginDirectory, "main.js"))).not.toHaveLength(0);
    expect(await readFile(path.join(pluginDirectory, "manifest.json"))).not.toHaveLength(0);
    expect(await readFile(path.join(pluginDirectory, "styles.css"))).not.toHaveLength(0);
    expect(await readFile(path.join(pluginDirectory, "data.json"))).toEqual(savedSettings);
    expect((await readdir(pluginDirectory)).sort()).toEqual([
      "data.json",
      "main.js",
      "manifest.json",
      "styles.css",
    ]);
  });

  it("requires an absolute Vault path", async () => {
    await expect(installVault("relative/vault")).rejects.toThrow(
      "Vault path must be absolute",
    );
  });

  it("requires the Vault to contain an .obsidian directory", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "yield-wiki-not-vault-"));
    temporaryDirectories.push(directory);

    await expect(installVault(directory)).rejects.toThrow(
      "Vault must contain an .obsidian directory",
    );
  });
});
