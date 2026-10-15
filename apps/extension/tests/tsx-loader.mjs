/**
 * 组件测试用的最小 TSX 加载器。
 *
 * `node --test` 只原生支持 `.ts` 的类型擦除，不认 `.tsx`。这里用 TypeScript
 * 自己的 transpileModule 把 `.tsx` 转成 CommonJS 再放进 vm 执行，依赖解析
 * 递归走同一套规则，因此组件引用的 `.ts` / `.tsx` 都能被加载。
 */
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const cache = new Map();

export function loadComponent(file) {
  if (cache.has(file)) return cache.get(file).exports;
  const module = { exports: {} };
  cache.set(file, module);
  const output = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const localRequire = (specifier) => {
    if (specifier.endsWith(".css")) return {};
    if (!specifier.startsWith(".")) return require(specifier);
    let target = path.resolve(path.dirname(file), specifier);
    if (!path.extname(target)) {
      target = [".ts", ".tsx"].map((extension) => target + extension).find(existsSync);
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
