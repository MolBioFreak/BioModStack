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
await writeFile('dist/vendor/msa.js', source.replace(old, 'typeof t.on==="function"'))
