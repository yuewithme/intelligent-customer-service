import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'uno.css'
import './styles/app.css'
import App from './App.vue'
import router from './router'
import ContentWrap from './components/ContentWrap.vue'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.component('ContentWrap', ContentWrap)
app.mount('#app')
