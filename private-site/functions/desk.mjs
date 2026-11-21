import { getStore } from '@netlify/blobs';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { privateDesk } from '../desk.mjs';

const bundle = resolve('bundle');
const collector = readFileSync(resolve(bundle,'collector-url.txt'),'utf8').trim();
export default function handler(request, context) {
  return privateDesk({
    sessions:getStore({name:'private-desk-sessions',consistency:'strong'}),
    ownerEmail:process.env.LAB_OWNER_EMAIL?.trim().toLowerCase(), ownerId:process.env.LAB_OWNER_ID,
    identity:'https://' + context.site.name + '.netlify.app/.netlify/identity',
    collector, collectorKey:process.env.LAB_COLLECTOR_READ_KEY, bundle
  })(request);
}
export const config = {path:'/*', excludedPath:['/.netlify/*','/auth/login','/auth/invite'], preferStatic:false};
