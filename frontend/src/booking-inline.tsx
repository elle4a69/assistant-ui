import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import CustomerBookingView from './CustomerBookingView'
import bookingStyles from './index.css?inline'

const host = document.getElementById('booking-container')

if (!host) {
  throw new Error('Inline booking container was not found')
}

const shadowRoot = host.shadowRoot ?? host.attachShadow({ mode: 'open' })
const style = document.createElement('style')
style.textContent = `
  ${bookingStyles}

  :host {
    display: block;
    width: 100%;
    color-scheme: light;
    background: transparent;
  }

  #booking-inline-root {
    position: relative;
    width: 100%;
    overflow: visible;
    background: transparent;
  }
`

const mount = document.createElement('div')
mount.id = 'booking-inline-root'
shadowRoot.replaceChildren(style, mount)

createRoot(mount).render(
  <StrictMode>
    <CustomerBookingView embedded />
  </StrictMode>,
)
