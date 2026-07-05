import '@testing-library/jest-dom'

// jsdom doesn't implement scrollIntoView or scrollTo
window.HTMLElement.prototype.scrollIntoView = () => {}
window.HTMLElement.prototype.scrollTo = () => {}

// Node 26 + jsdom 29 leave window.localStorage undefined unless
// --localstorage-file is passed. Polyfill a minimal in-memory Storage so
// tests can use the real localStorage API instead of stubbing it per-file.
if (typeof window.localStorage === 'undefined') {
  class MemoryStorage implements Storage {
    private store = new Map<string, string>()
    get length() { return this.store.size }
    clear() { this.store.clear() }
    getItem(key: string) { return this.store.has(key) ? this.store.get(key)! : null }
    key(index: number) { return Array.from(this.store.keys())[index] ?? null }
    removeItem(key: string) { this.store.delete(key) }
    setItem(key: string, value: string) { this.store.set(key, String(value)) }
  }
  Object.defineProperty(window, 'localStorage', { value: new MemoryStorage() })
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage })
}
