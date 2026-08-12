import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import AnonPage from './anon/AnonPage.tsx'
import './index.css'

const Root = window.location.pathname === '/anon' || window.location.pathname.startsWith('/anon/')
  ? AnonPage
  : App

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
