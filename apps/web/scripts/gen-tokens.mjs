// The platform's own colours come from its Creative Studio colour system (WCAG-checked).
//   PYTHON=... node scripts/gen-tokens.mjs [#brand]
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const brand = process.argv[2] ?? "#1f6feb";
const code = `
import sys
from novara.creative.color import build_system, to_css
s = build_system(sys.argv[1])
assert s["all_checks_pass"], [c for c in s["checks"] if not c["passed"]]
print(to_css(s), end="")
`;
const css = execFileSync(process.env.PYTHON ?? "python3", ["-c", code, brand], {
  cwd: path.resolve(web, "../../services/api"),
  encoding: "utf8",
});
writeFileSync(path.join(web, "src/styles/tokens.css"), css);
console.log(`tokens.css generated for ${brand}`);
