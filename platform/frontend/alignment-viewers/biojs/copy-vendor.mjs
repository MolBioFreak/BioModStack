import { mkdir, copyFile, readFile, writeFile, rm } from 'node:fs/promises'
await rm('dist', { recursive: true, force: true })
await mkdir('dist/vendor', { recursive: true })
await copyFile('node_modules/msa/dist/msa.css', 'dist/vendor/msa.css')
await copyFile('node_modules/msa/LICENSE', 'dist/vendor/msa.LICENSE')
// stat.seqs in this published bundle treats any object with .at as a Backbone
// collection. Modern arrays also have .at; test for its event API instead.
const path = 'node_modules/msa/dist/msa.js'
const source = await readFile(path, 'utf8')
const old = '!t instanceof Array||"at"in t'
if (source.split(old).length !== 2) throw new Error('Unexpected msa bundle; review compatibility patch')
// The Gecko redraw path references `_`, although this module imports lodash as
// `s`. Keep the native throttle, including after a zoom rebuilds its canvas.
const brokenThrottle = '_.throttle(this.draw,e)'
if (source.split(brokenThrottle).length !== 2 || !source.includes('(0,s.throttle)(this.throttledDraw,30)')) throw new Error('Unexpected msa Gecko throttle; review compatibility patch')
await writeFile('dist/vendor/msa.js', source.replace(old, 'typeof t.on==="function"').replace(brokenThrottle, 's.throttle(this.draw,e)'))
