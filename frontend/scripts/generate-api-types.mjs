import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const frontendDir = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(frontendDir, "..");
const backendDir = resolve(repoRoot, "backend");
const output = resolve(frontendDir, "src/lib/openapi-types.ts");
const tempDir = mkdtempSync(join(tmpdir(), "basketball-data-emporium-openapi-"));
const specPath = join(tempDir, "openapi.json");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) {
    console.error(`Command failed: ${command} ${args.join(" ")}`);
    if (result.error) {
      console.error(result.error);
    }
    process.exit(result.status ?? 1);
  }
}

function runCommandLine(commandLine, options = {}) {
  const result = spawnSync("cmd.exe", ["/d", "/s", "/c", commandLine], {
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) {
    console.error(`Command failed: ${commandLine}`);
    if (result.error) {
      console.error(result.error);
    }
    process.exit(result.status ?? 1);
  }
}

try {
  run(
    "uv",
    [
      "run",
      "python",
      "-c",
      [
        "import json, os",
        "from fastapi.testclient import TestClient",
        "from basketball_data_emporium.server.app import app",
        "spec = TestClient(app).get('/openapi.json').json()",
        "open(os.environ['OPENAPI_SPEC_PATH'], 'w', encoding='utf-8').write(json.dumps(spec))",
      ].join("; "),
    ],
    {
      cwd: backendDir,
      env: { ...process.env, OPENAPI_SPEC_PATH: specPath },
    },
  );

  const localCli = resolve(frontendDir, "node_modules/openapi-typescript/bin/cli.js");
  if (existsSync(localCli)) {
    run("node", [localCli, specPath, "-o", output], { cwd: frontendDir });
  } else if (process.platform === "win32") {
    runCommandLine(`npx --yes openapi-typescript ${specPath} -o ${output}`, {
      cwd: frontendDir,
    });
  } else {
    run("npx", ["--yes", "openapi-typescript", specPath, "-o", output], {
      cwd: frontendDir,
    });
  }
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
