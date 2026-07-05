import '@testing-library/jest-dom'

// jsdom doesn't implement scrollIntoView or scrollTo
window.HTMLElement.prototype.scrollIntoView = () => {}
window.HTMLElement.prototype.scrollTo = () => {}
