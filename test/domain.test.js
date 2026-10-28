import test from 'node:test';import assert from 'node:assert/strict';
import {initial,decide,propose,saveRule,nextCase,policy} from './phase1-domain.js';
test('approval prepares a note and writes an audit',()=>{const s=initial();decide(s,'psx','approve');assert.equal(s.events[0].status,'handled');assert.equal(s.audit.length,1);assert.match(s.audit[0].detail,/No order/);});
test('rejection and duplicate prevention',()=>{const s=initial();decide(s,'psx','reject');assert.equal(s.events[0].status,'rejected');assert.throws(()=>decide(s,'psx','approve'));});
test('correction and candidate grant no authority',()=>{const s=initial();propose(s,'do anything');assert.equal(s.rules.length,0);assert.equal(s.candidate.active,false);assert.equal(s.events[0].status,'needs');});
test('explicit rule approval then later matching application',()=>{const s=initial();propose(s,'3% / 20%');saveRule(s);nextCase(s);assert.equal(s.events[0].symbol,'VLO');assert.equal(s.events[0].status,'handled');assert.equal(s.rules[0].uses,1);assert.throws(()=>nextCase(s));assert.equal(s.audit.length,4);});
test('paused or revoked standing rule removes authority',()=>{for(const revoke of [false,true]){const s=initial();propose(s,'demo');saveRule(s);if(revoke)s.rules=[];else s.rules[0].active=false;nextCase(s);assert.equal(s.events[0].status,'needs');}});
test('hard policy overrides approval',()=>{const s=initial();s.events[0].position=7;assert.throws(()=>decide(s,'psx','approve'));assert.equal(s.events[0].status,'needs');});
test('freshness and independent uncertainty fail closed',()=>{for(const change of [{fresh:false},{challenge:'uncertain'},{research:'failure'}]){const s=initial();Object.assign(s.events[0],change);assert.throws(()=>decide(s,'psx','approve'));}});
test('invalid numerical state fails closed',()=>{const e=initial().events[0];for(const position of [NaN,Infinity,-1,0,'2'])assert.equal(policy({...e,position}),false);});
test('declined rule does not affect source case',()=>{const s=initial();propose(s,'demo');s.candidate=null;assert.throws(()=>saveRule(s));assert.equal(s.events[0].status,'needs');});
test('browser serialization preserves rule-to-decision chain',()=>{const s=initial();propose(s,'demo');saveRule(s);const restored=JSON.parse(JSON.stringify(s));nextCase(restored);assert.equal(restored.events[0].ruleId,restored.rules[0].id);});
test('deterministic demo fixtures',()=>assert.deepEqual(initial(),initial()));

