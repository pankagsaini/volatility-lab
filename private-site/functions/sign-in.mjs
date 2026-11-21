import desk from './desk.mjs';
export default desk;
export const config = {
  path:['/auth/login','/auth/invite'], method:'POST',
  rateLimit:{action:'rate_limit',aggregateBy:['ip','domain'],windowLimit:12,windowSize:60}
};
