// Regenerate the typed API contract from the backend:
//   services/api (FastAPI) -> openapi.json -> src/api/schema.d.ts
// Set PYTHON to the interpreter that has the backend's requirements installed.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "..");
const api = path.resolve(web, "../../services/api");
const python = process.env.PYTHON ?? "python3";

const spec = execFileSync(python, ["-m", "scripts.export_openapi"], { cwd: api, encoding: "utf8", maxBuffer: 64 << 20 });
writeFileSync(path.join(web, "openapi.json"), spec);
execFileSync(path.join(web, "node_modules/.bin/openapi-typescript"), ["openapi.json", "-o", "src/api/schema.d.ts", "--empty-objects-unknown"], {
  cwd: web,
  stdio: "inherit",
});
