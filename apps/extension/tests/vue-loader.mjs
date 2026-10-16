/** 使用与 Vite 相同的 Vue 编译器加载 SFC，供真实 Vue SSR 测试使用。 */
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import vm from "node:vm";
import ts from "typescript";
import { parse, compileScript } from "@vue/compiler-sfc";

const require = createRequire(import.meta.url);
const cache = new Map();

export function loadComponent(file) {
  if (cache.has(file)) return cache.get(file).exports;
  const module = { exports: {} };
  cache.set(file, module);
  let source = readFileSync(file, "utf8");
  if (file.endsWith(".vue")) {
    const { descriptor } = parse(source, { filename: file });
    source = compileScript(descriptor, { id: file, inlineTemplate: true,
      fs: {fileExists: existsSync, readFile: filename => readFileSync(filename, "utf8")},
    }).content;
  }
  const output = ts.transpileModule(source, {
    fileName: file.endsWith(".vue") ? "component.ts" : file,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = (specifier) => {
    if (specifier.endsWith(".css")) return {};
    if (/\.(png|jpg|svg)$/.test(specifier)) return specifier;
    if (!specifier.startsWith(".")) return require(specifier);
    let target = path.resolve(path.dirname(file), specifier);
    if (!path.extname(target)) {
      target = [".ts", ".vue"].map((extension) => target + extension).find(existsSync);
    }
    return loadComponent(target);
  };
  vm.runInNewContext(output, { require: localRequire, module, exports: module.exports });
  return module.exports;
}

/** 加载相对于本仓库 `src/` 的组件模块。 */
export function loadSource(relativePath, base) {
  return loadComponent(
    new URL(`../src/${relativePath}`, base ?? import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  );
}
