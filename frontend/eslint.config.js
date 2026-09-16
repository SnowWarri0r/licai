import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],

      // 这两条不是"关掉不管", 是把它们从 error 降到与后果相称的级别 ——
      // 一屏 error 里如果有三成永远不会有人去动, 整个 lint 输出就会被习惯性略过,
      // 真问题(下面那几条 react-hooks)反而看不见。
      //
      // allowEmptyCatch: 本仓到处是 `try { ... } catch {}` 的尽力而为写法
      // (取数失败就保留旧值, 不弹错)。这是刻意的, 不是漏写。非 catch 的空块照旧报错。
      'no-empty': ['error', { allowEmptyCatch: true }],
      // only-export-components: 只影响 Fast Refresh —— 命中的文件改动时会整页重载
      // 而不是热替换, 零运行时影响。该拆常量的地方已经拆了(kline/shared.js、
      // portfolio/constants.js), 剩下的拆出去就是为两行代码建一个文件。留成 warn 可见。
      'react-refresh/only-export-components': 'warn',
    },
  },
])
