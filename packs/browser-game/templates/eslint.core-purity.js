// The purity rules for src/core, the part of {{NAME}} that holds the rules of the game.
//
// The core must stay pure TypeScript: no engine library, no DOM, no host platform, no
// Math.random. Everything it needs arrives as data, and it emits events out.
//
// Spread the default export into the project's own eslint.config.js, after the general blocks:
//
//   import corePurity from './eslint.core-purity.js';
//
//   export default tseslint.config(
//     js.configs.recommended,
//     ...tseslint.configs.recommended,
//     ...corePurity,
//   );
//
// The named export `corePurityConfig` is the one config object, for a project that lists its
// blocks by hand. Add the project's engine library to the first pattern group when it uses
// another one.

export const corePurityConfig = {
  files: ['src/core/**/*.ts'],
  languageOptions: {
    globals: {}, // no window, no document, no process
  },
  rules: {
    'no-restricted-imports': ['error', {
      patterns: [
        { group: ['phaser', 'phaser/*'], message: 'src/core must not depend on the renderer.' },
        { group: ['**/view/**'], message: 'src/core must not depend on the renderer.' },
        { group: ['node:*', 'fs', 'path'], message: 'src/core must not depend on the host platform.' },
      ],
    }],
    'no-restricted-globals': ['error',
      { name: 'window', message: 'src/core must not touch the DOM.' },
      { name: 'document', message: 'src/core must not touch the DOM.' },
      { name: 'localStorage', message: 'src/core must not touch the DOM.' },
      { name: 'process', message: 'src/core must not touch the host platform.' },
    ],
    'no-restricted-properties': ['error',
      { object: 'Math', property: 'random', message: 'Use a named RNG stream; the simulation must stay deterministic.' },
    ],
  },
};

export default [corePurityConfig];
