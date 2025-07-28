import Alpine from 'https://unpkg.com/alpinejs@3.x.x/dist/module.esm.js'

document.addEventListener('alpine:init', () => {
  console.log('alpine:init event fired')
})

Alpine.data('ticketsApp', () => ({
  tickets: [],
  timeAgo(dateString) {
    if (!dateString) return ''
    const now = new Date()
    const updated = new Date(dateString)
    const diffSec = Math.round((now - updated) / 1000)
    if (diffSec < 60) return `${diffSec} seconds ago`
    const diffMin = Math.round(diffSec / 60)
    if (diffMin < 60) return `${diffMin} minutes ago`
    const diffHrs = Math.round(diffMin / 60)
    if (diffHrs < 24) return `${diffHrs} hours ago`
    return `${Math.round(diffHrs / 24)} days ago`
  },
  isOverdue(dateString) {
    if (!dateString) return false
    const due = new Date(dateString + 'T00:00:00')
    return due < new Date()
  },
  daysOld(dateString) {
    return Math.floor((Date.now() - new Date(dateString)) / (1000 * 60 * 60 * 24))
  },
  async load() {
    try {
      const res = await fetch('/in-progress')
      this.tickets = res.ok ? await res.json() : []
    } catch (err) {
      console.error('Failed to load tickets:', err)
    }
  },
  init() {
    this.load()
    setInterval(() => this.load(), 10000)
  }
}))

Alpine.data('dueApp', () => ({
  tickets: [],
  timeAgo(dateString) {
    const now = new Date()
    const updated = new Date(dateString)
    const diffSec = Math.round((now - updated) / 1000)
    if (diffSec < 60) return `${diffSec} seconds ago`
    const diffMin = Math.round(diffSec / 60)
    if (diffMin < 60) return `${diffMin} minutes ago`
    const diffHrs = Math.round(diffMin / 60)
    if (diffHrs < 24) return `${diffHrs} hours ago`
    return `${Math.round(diffHrs / 24)} days ago`
  },
  isOverdue(dateString) {
    if (!dateString) return false
    const due = new Date(dateString + 'T00:00:00')
    return due < new Date()
  },
  async load() {
    try {
      const res = await fetch('/open-issues-by-due')
      this.tickets = res.ok ? await res.json() : []
    } catch (err) {
      console.error('Failed to load due-date tickets:', err)
    }
  },
  init() {
    this.load()
    setInterval(() => this.load(), 10000)
  }
}))

Alpine.data('reposApp', () => ({
  repos: [],
  async load() {
    try {
      const res = await fetch('/repos')
      this.repos = res.ok ? await res.json() : []
    } catch (err) {
      console.error('Failed to load repos:', err)
    }
  },
  init() {
    console.log("reposApp init()");
    this.load()
  }
}))

Alpine.data('deploymentsApp', () => ({
  deployments: [],
  timeAgo(dateString) {
    const now = new Date()
    const updated = new Date(dateString)
    const diffSec = Math.round((now - updated) / 1000)
    if (diffSec < 60) return `${diffSec} seconds ago`
    const diffMin = Math.round(diffSec / 60)
    if (diffMin < 60) return `${diffMin} minutes ago`
    const diffHrs = Math.round(diffMin / 60)
    if (diffHrs < 24) return `${diffHrs} hours ago`
    return `${Math.round(diffHrs / 24)} days ago`
  },
  badgeClass(result) {
    const res = (result || '').toUpperCase()
    switch (res) {
      case 'SUCCESSFUL':
      case 'COMPLETED':
        return 'badge bg-success'
      case 'FAILED':
      case 'ERROR':
      case 'FAILED_WITH_ERRORS':
        return 'badge bg-danger'
      case 'STOPPED':
      case 'CANCELLED':
        return 'badge bg-secondary'
      case 'IN_PROGRESS':
        return 'badge bg-warning text-dark'
      default:
        return 'badge bg-info'
    }
  },
  async load() {
    try {
      const res = await fetch('/deployments')
      if (res.ok) {
        this.deployments = await res.json()
      } else {
        const txt = await res.text()
        console.error('Failed to load deployments:', res.status, txt)
        this.deployments = []
      }
    } catch (err) {
      console.error('Failed to load deployments:', err)
    }
  },
  init() {
    this.load()
    setInterval(() => this.load(), 30000)
  }
}))

Alpine.start()
