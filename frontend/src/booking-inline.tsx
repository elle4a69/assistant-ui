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
    min-height: 800px;
    color-scheme: light;
    background: #faf6f6;
  }

  #booking-inline-root {
    position: relative;
    width: 100%;
    min-height: 800px;
    overflow: visible;
  }

  @media (max-width: 719px) {
    :host,
    #booking-inline-root {
      min-height: 760px;
    }
  }
`

const mount = document.createElement('div')
mount.id = 'booking-inline-root'
shadowRoot.replaceChildren(style, mount)

createRoot(mount).render(
  <StrictMode>
    <CustomerBookingView />
  </StrictMode>,
)
