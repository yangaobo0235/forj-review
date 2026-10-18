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
const { parse } = require("@vue/compiler-sfc");
const findings = [];

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : /\.(tsx?|jsx?|mjs|vue)$/.test(file) ? [file] : [];
  });
}

function scriptSource(file) {
  const content = fs.readFileSync(file, "utf8");
  if (!file.endsWith(".vue")) return content;
  const { descriptor, errors } = parse(content, {filename: file});
  if (errors.length) findings.push(`${file}: invalid Vue component`);
  return [descriptor.script?.content, descriptor.scriptSetup?.content].filter(Boolean).join("\n");
}

// Keep both independently built frontends on Vue SFCs and plain TypeScript.
const forbiddenFramework = /^(react(?:-dom|-test-renderer|-refresh)?|@types\/react(?:-dom)?|@vitejs\/plugin-react(?:-swc)?|@vitejs\/plugin-vue-jsx|@vue\/babel-plugin-jsx)(?:\/|$)/;
for (const app of ["extension", "configuration-web"]) {
  const directory = path.join(root, "apps", app);
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, "package.json"), "utf8"));
  for (const dependency of Object.keys({...manifest.dependencies, ...manifest.devDependencies})) {
    if (forbiddenFramework.test(dependency)) findings.push(`${app}: unsupported frontend dependency: ${dependency}`);
  }
  for (const folder of ["src", "tests"]) {
    const target = path.join(directory, folder);
    if (!fs.existsSync(target)) continue;
    for (const file of walk(target)) {
      const relative = `${app}/${path.relative(directory, file).replaceAll("\\", "/")}`;
      if (/\.[jt]sx$/.test(file)) findings.push(`${relative}: use a .vue component instead of JSX`);
      if (file.endsWith(".vue")) {
        const {descriptor} = parse(fs.readFileSync(file, "utf8"), {filename: file});
        if ([descriptor.script?.lang, descriptor.scriptSetup?.lang].some(lang => /^(tsx|jsx)$/.test(lang ?? ""))) {
          findings.push(`${relative}: Vue components must not use JSX scripts`);
        }
      }
      const source = ts.createSourceFile(file, scriptSource(file), ts.ScriptTarget.Latest, true);
      const visit = node => {
        const specifier = ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier)
          || (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || node.expression.getText(source) === "require") && node.arguments[0]);
        if (specifier && ts.isStringLiteral(specifier) && forbiddenFramework.test(specifier.text)) {
          findings.push(`${relative}: unsupported frontend import: ${specifier.text}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
  }
}

for (const file of walk(src)) {
  const relative = path.relative(src, file).replaceAll("\\", "/");
  const source = ts.createSourceFile(file, scriptSource(file), ts.ScriptTarget.Latest, true);
  const visit = node => {
    let specifier;
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      specifier = node.moduleSpecifier.text;
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
      specifier = node.argument.literal.text;
    }
    if (specifier?.startsWith(".")) {
      const target = path.resolve(path.dirname(file), specifier);
      const resolved = [target, `${target}.ts`, `${target}.vue`, path.join(target, "index.ts")]
        .find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
      const dependency = path.relative(src, target).replaceAll("\\", "/");
      let reason;
      if (!resolved) reason = "unresolved relative import";
      else if (dependency.startsWith("../")) reason = "product code imports outside src";
      else if (relative.startsWith("shared/") && !dependency.startsWith("shared/")) reason = "shared code imports a feature or platform";
      else if (relative.startsWith("platform/") && !/^(platform|shared)\//.test(dependency)) reason = "platform imports application UI or feature";
      else if (relative.startsWith("features/") && /^(app|entrypoints)\//.test(dependency)) reason = "feature imports an entrypoint";
      else if (relative === "platform/browser/actions/page-field-writer.ts"
        && dependency.includes("vehicle-model")) reason = "generic field writer imports vehicle model logic";
      if (reason) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart());
        findings.push(`${relative}:${line + 1}: ${reason}: ${specifier}`);
      }
    }
    if (relative.startsWith("platform/") && /^(vue|@vue\/|react)/.test(specifier ?? "")) findings.push(`${relative}: platform imports a UI framework`);
    if (relative === "platform/browser/actions/page-field-writer.ts" && ts.isStringLiteral(node)
      && node.text === "vehicle.model") findings.push(`${relative}: generic field writer embeds a vehicle model field`);
    ts.forEachChild(node, visit);
  };
  visit(source);
}

const webSrc = path.join(root, "apps/configuration-web/src");
if (fs.existsSync(webSrc)) {
  for (const file of walk(webSrc)) {
    const relative = path.relative(webSrc, file).replaceAll("\\", "/");
    const content = scriptSource(file);
    const source = ts.createSourceFile(file, content, ts.ScriptTarget.Latest, true);
    const visit = node => {
      if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
        const specifier = node.moduleSpecifier.text;
        if (specifier.startsWith(".") && !path.resolve(path.dirname(file), specifier).startsWith(webSrc + path.sep)) {
          findings.push(`configuration-web/${relative}: imports outside the web app: ${specifier}`);
        }
        if (specifier.includes("extension") || (specifier.startsWith("@forj/") && specifier !== "@forj/configuration-contracts")) {
          findings.push(`configuration-web/${relative}: imports another frontend implementation: ${specifier}`);
        }
      }
      if (ts.isPropertyAccessExpression(node) && node.expression.getText(source) === "chrome" && !relative.startsWith("browser/")) {
        findings.push(`configuration-web/${relative}: Chrome API access must use the browser bridge`);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
}
const contracts = path.join(root, "packages/configuration-contracts/src");
if (fs.existsSync(contracts)) {
  for (const file of walk(contracts)) {
    const source = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
    for (const node of source.statements) {
      if (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)
          || (ts.isExportDeclaration(node) && node.isTypeOnly)
          || (ts.isImportDeclaration(node) && node.importClause?.isTypeOnly)) continue;
      findings.push(`configuration-contracts/${path.basename(file)}: shared contracts contain runtime behavior`);
    }
  }
}
console.log(findings.length ? findings.join("\n") : "Frontend architecture: OK (extension, configuration-web, contracts)");
process.exitCode = findings.length ? 1 : 0;
