import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import vue from 'eslint-plugin-vue'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {files: ['**/*.{ts,vue}'], extends: [js.configs.recommended, tseslint.configs.recommended], languageOptions: {globals: globals.browser}},
  ...vue.configs['flat/essential'],
  {files: ['**/*.vue'], languageOptions: {parserOptions: {parser: tseslint.parser, extraFileExtensions: ['.vue']}}},
])
