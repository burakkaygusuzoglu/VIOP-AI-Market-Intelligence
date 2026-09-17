import '@testing-library/jest-dom/vitest';

/*
 * jsdom implements neither `URL.createObjectURL` nor `URL.revokeObjectURL`.
 *
 * They are defined here rather than in each test because the gap is the
 * environment's, not any one test's: a component that previews a chosen file
 * uses them, and without these a render throws "createObjectURL is not a
 * function" before any assertion runs. Tests that care about the calls spy on
 * these, which `vi.spyOn` can only do once the properties exist.
 */
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:jsdom';
}
if (typeof URL.revokeObjectURL !== 'function') {
  URL.revokeObjectURL = () => undefined;
}
