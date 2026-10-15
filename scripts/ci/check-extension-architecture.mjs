/* Check TypeScript imports without requiring the extension's runtime or DOM. */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const extension = path.join(root, "apps/extension");
const src = path.join(extension, "src");
const require = createRequire(path.join(extension, "package.json"));
const ts = require("typescript");
const findings = [];

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : /\.tsx?$/.test(file) ? [file] : [];
  });
}

for (const file of walk(src)) {
  const relative = path.relative(src, file).replaceAll("\\", "/");
  const source = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const visit = node => {
    let specifier;
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      specifier = node.moduleSpecifier.text;
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
      specifier = node.argument.literal.text;
    }
    if (specifier?.startsWith(".")) {
      const target = path.resolve(path.dirname(file), specifier);
      const resolved = [target, `${target}.ts`, `${target}.tsx`, path.join(target, "index.ts")]
        .find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
      const dependency = path.relative(src, target).replaceAll("\\", "/");
      let reason;
      if (!resolved) reason = "unresolved relative import";
      else if (dependency.startsWith("../")) reason = "product code imports outside src";
      else if (relative.startsWith("shared/") && !dependency.startsWith("shared/")) reason = "shared code imports a feature or platform";
      else if (relative.startsWith("platform/") && !/^(platform|shared)\//.test(dependency)) reason = "platform imports application UI or feature";
      else if (relative.startsWith("features/") && /^(app|entrypoints)\//.test(dependency)) reason = "feature imports an entrypoint";
      if (reason) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart());
        findings.push(`${relative}:${line + 1}: ${reason}: ${specifier}`);
      }
    }
    if (relative.startsWith("platform/") && specifier === "react") findings.push(`${relative}: platform imports React`);
    ts.forEachChild(node, visit);
  };
  visit(source);
}

console.log(findings.length ? findings.join("\n") : "Extension architecture: OK");
process.exitCode = findings.length ? 1 : 0;
