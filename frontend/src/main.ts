import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import App from './App.vue'
import { router } from './lib/routes'
import { applyInitialTheme } from './lib/theme'
import './styles/base.css'

// Apply the persisted theme to <html data-theme> before the app mounts
// so the first paint already has the right palette (no FOUC).
applyInitialTheme()

const app = createApp(App)

app.use(createPinia())
app.use(PiniaColada)
app.use(router)

app.mount('#app')
