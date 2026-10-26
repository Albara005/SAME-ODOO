import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// An effect's return value is its cleanup, which React calls later. An arrow without braces returns whatever its
// call returns: newer browsers' scrollTo and scrollIntoView return a promise, and calling it crashed the page.
it("effects have braces, so they return nothing but a cleanup", () => {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) files.push(path);
    }
  };
  walk(join(__dirname, ".."));
  const offenders = files.flatMap((f) =>
    readFileSync(f, "utf8").split("\n").flatMap((line, i) => (/use(Layout)?Effect\(\(\) => [^{]/.test(line) ? [`${f}:${i + 1}`] : [])),
  );
  expect(offenders).toEqual([]);
});
