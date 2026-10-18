/**
 * Vitest setup.
 *
 * `globals: false` means Testing Library cannot register its automatic cleanup,
 * so it is done here once for every test file. Without it, a second `render` in
 * the same file leaves the first tree mounted and queries match twice.
 */

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})

// jsdom has no matchMedia. The sidebar asks it whether the viewport is mobile (a browser always
// has it), so give it a stub that reports "not matching" unless a test overrides it.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList
}

// jsdom has no ResizeObserver either; Radix's menus measure their trigger with it. A no-op
// observer is enough: tests check behaviour, not layout.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
}
