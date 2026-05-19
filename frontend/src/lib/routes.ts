import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from 'vue-router'

// MANDATE 1 — vue-router is v5 (not v4). `createRouter` +
// `createWebHistory` are non-breaking between v4 and v5, so we use the
// v5 API directly with no compat shims.
//
// All views are lazy-loaded (`() => import(...)`) so each becomes its
// own chunk — keeps the initial bundle small (Phase 4 <800KB gz target).
export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'hub',
    component: () => import('../views/HubView.vue'),
  },
  {
    path: '/projects/:id',
    name: 'project',
    component: () => import('../views/ProjectView.vue'),
    props: true,
  },
  {
    path: '/projects/:id/new-run',
    name: 'new-run',
    component: () => import('../views/NewRunWizard.vue'),
    props: true,
  },
  {
    path: '/runs/:id',
    name: 'run-detail',
    component: () => import('../views/RunDetailView.vue'),
    props: true,
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
