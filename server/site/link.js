'use strict'

const form = document.getElementById('linkForm')
const message = document.getElementById('linkMessage')
form.addEventListener('submit', async event => {
  event.preventDefault()
  const button = form.querySelector('button')
  button.disabled = true
  message.textContent = 'Linking…'
  try {
    const response = await fetch('api/pair', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: document.getElementById('linkCode').value })
    })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || 'Linking failed')
    location.reload()
  } catch (error) {
    message.textContent = error.message
    button.disabled = false
  }
})
