import process from 'node:process';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);

const REQUIRED_REFERENCES = [
  'typescript/package.json',
  'vite/package.json',
  '@vitejs/plugin-react/package.json',
  '@tailwindcss/vite/package.json',
  'vite/client'
];

function canResolve(reference) {
  try {
    require.resolve(reference, { paths: [process.cwd()] });
    return true;
  } catch {
    return false;
  }
}

function getMissingReferences() {
  return REQUIRED_REFERENCES.filter((reference) => !canResolve(reference));
}

function runInstall() {
  const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const result = spawnSync(npmCmd, ['ci', '--include=dev'], {
    cwd: process.cwd(),
    stdio: 'inherit',
    env: process.env
  });

  if (result.error) {
    throw result.error;
  }
  if (typeof result.status === 'number' && result.status !== 0) {
    process.exit(result.status);
  }
}

function main() {
  const missingBefore = getMissingReferences();
  if (missingBefore.length === 0) {
    console.log('[ensure-build-deps] Build dependencies are already installed.');
    return;
  }

  console.warn(
    `[ensure-build-deps] Missing build dependencies detected (${missingBefore.join(
      ', '
    )}). Running "npm ci --include=dev"...`
  );
  runInstall();

  const missingAfter = getMissingReferences();
  if (missingAfter.length > 0) {
    console.error(
      `[ensure-build-deps] Dependencies are still missing after install: ${missingAfter.join(', ')}`
    );
    process.exit(1);
  }

  console.log('[ensure-build-deps] Build dependencies installed successfully.');
}

main();
