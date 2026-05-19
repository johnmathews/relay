import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { PiniaColada } from '@pinia/colada'
import App from './App.vue'
import { router } from './lib/routes'
import './styles/base.css'

const app = createApp(App)

app.use(createPinia())
app.use(PiniaColada)
app.use(router)

app.mount('#app')
