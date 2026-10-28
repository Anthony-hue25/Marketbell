import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
const candidates=[process.env.MARKETBELL_PYTHON,'.venv/Scripts/python.exe','.venv/bin/python','C:/Users/antho/adaptive-universe-lab/.venv/Scripts/python.exe'].filter(Boolean);
const python=candidates.find(p=>existsSync(p))||'python';
const child=spawn(python,['-B','-u','-m','backend.main'],{stdio:'inherit',cwd:new URL('.',import.meta.url),env:process.env});
child.on('error',()=>{console.error('Python runtime unavailable. See README setup.');process.exit(1);});
child.on('exit',code=>process.exit(code??1));
process.on('SIGINT',()=>child.kill('SIGINT'));process.on('SIGTERM',()=>child.kill('SIGTERM'));
