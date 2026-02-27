import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { createRequire } from 'node:module';

const projectRoot = process.cwd();
const require = createRequire(import.meta.url);
const strictEnv = process.argv.includes('--strict-env');

function fail(message) {
  console.error(`[predeploy] ERROR: ${message}`);
  process.exit(1);
}

function readJson(filePath) {
  const raw = fs.readFileSync(filePath, 'utf-8');
  return JSON.parse(raw);
}

function checkNodeVersion() {
  const major = Number.parseInt(String(process.versions.node || '0').split('.')[0] || '0', 10);
  if (!Number.isFinite(major) || major < 20 || major >= 21) {
    fail(`Node.js 版本必须是 20.x，当前为 ${process.versions.node}`);
  }
  console.log(`[predeploy] Node.js 版本检查通过: ${process.versions.node}`);
}

function checkNoFileDependency(pkg) {
  const sections = ['dependencies', 'devDependencies', 'optionalDependencies'];
  for (const section of sections) {
    const deps = pkg[section] && typeof pkg[section] === 'object' ? pkg[section] : {};
    for (const [name, version] of Object.entries(deps)) {
      const value = String(version || '').trim();
      if (value.startsWith('file:')) {
        fail(`检测到本地依赖 ${section}.${name}=${value}，云构建不允许使用 file: 依赖`);
      }
    }
  }
  console.log('[predeploy] package.json 依赖检查通过（无 file: 依赖）');
}

function checkTypesResolvable(tsconfig) {
  const types = Array.isArray(tsconfig?.compilerOptions?.types) ? tsconfig.compilerOptions.types : [];
  if (types.length === 0) {
    console.log('[predeploy] tsconfig 未配置 compilerOptions.types，跳过类型引用解析检查');
    return;
  }
  const resolveByPackageSubpath = (ref) => {
    const segments = ref.split('/').filter(Boolean);
    if (segments.length < 2) return false;

    const packageName = ref.startsWith('@')
      ? segments.length >= 3
        ? `${segments[0]}/${segments[1]}`
        : ''
      : segments[0];
    if (!packageName) return false;

    const subpath = ref.startsWith('@')
      ? segments.slice(2).join('/')
      : segments.slice(1).join('/');
    if (!subpath) return false;

    let packageJsonPath = '';
    try {
      packageJsonPath = require.resolve(`${packageName}/package.json`, { paths: [projectRoot] });
    } catch {
      return false;
    }
    const packageRoot = path.dirname(packageJsonPath);
    const candidates = [
      path.join(packageRoot, `${subpath}.d.ts`),
      path.join(packageRoot, subpath, 'index.d.ts'),
      path.join(packageRoot, `${subpath}.ts`),
      path.join(packageRoot, subpath, 'index.ts'),
    ];
    return candidates.some((candidate) => fs.existsSync(candidate));
  };

  for (const typeName of types) {
    const ref = String(typeName || '').trim();
    if (!ref) continue;
    try {
      require.resolve(ref, { paths: [projectRoot] });
      console.log(`[predeploy] 类型引用可解析: ${ref}`);
    } catch (error) {
      if (resolveByPackageSubpath(ref)) {
        console.log(`[predeploy] 类型引用可解析: ${ref}`);
        continue;
      }
      fail(`无法解析 tsconfig 类型引用 "${ref}"，请确认依赖已安装`);
    }
  }
}

function checkApiBaseEnv() {
  const value = String(process.env.VITE_SUBTITLE_API_BASE || '').trim();
  if (!value) {
    fail('环境变量 VITE_SUBTITLE_API_BASE 为空');
  }
  if (!/^https?:\/\//i.test(value)) {
    fail(`VITE_SUBTITLE_API_BASE 必须是 http(s) URL，当前值: ${value}`);
  }
  let parsed = null;
  try {
    parsed = new URL(value);
  } catch {
    fail(`VITE_SUBTITLE_API_BASE 不是合法 URL，当前值: ${value}`);
  }
  const hostname = String(parsed.hostname || '').toLowerCase();
  if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '0.0.0.0') {
    fail(`生产部署禁止使用本地地址，当前 VITE_SUBTITLE_API_BASE=${value}`);
  }
  if (value.includes('<') || value.includes('>') || hostname === 'example.com') {
    fail(`VITE_SUBTITLE_API_BASE 仍是占位值，请替换为真实后端域名，当前值: ${value}`);
  }
  const pathName = String(parsed.pathname || '').replace(/\/+$/, '');
  if (!pathName.endsWith('/api/v1')) {
    fail(`VITE_SUBTITLE_API_BASE 必须以 /api/v1 结尾，当前值: ${value}`);
  }
  console.log(`[predeploy] 环境变量检查通过: VITE_SUBTITLE_API_BASE=${value}`);
}

function main() {
  const packageJsonPath = path.join(projectRoot, 'package.json');
  const tsconfigPath = path.join(projectRoot, 'tsconfig.json');
  if (!fs.existsSync(packageJsonPath)) fail('未找到 package.json');
  if (!fs.existsSync(tsconfigPath)) fail('未找到 tsconfig.json');

  const pkg = readJson(packageJsonPath);
  const tsconfig = readJson(tsconfigPath);

  checkNodeVersion();
  checkNoFileDependency(pkg);
  checkTypesResolvable(tsconfig);
  if (strictEnv) {
    checkApiBaseEnv();
  } else {
    console.log('[predeploy] 未启用 --strict-env，跳过 VITE_SUBTITLE_API_BASE 强校验');
  }

  console.log('[predeploy] 所有检查通过');
}

main();
