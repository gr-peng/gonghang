import {book, baseURL} from './api.js';
let sessionPromise, sessionBase;
export async function finance(path, options={}) {
  const base=baseURL('book');
  if(!sessionPromise || sessionBase!==base) {
    sessionBase=base;
    sessionPromise=book('/finance/session').catch(error=>{sessionPromise=null;throw error;});
  }
  const session=await sessionPromise;
  try {return await book('/finance'+path,{...options,headers:{...options.headers,'X-Qingcai-CSRF':session.csrf}});}
  catch(error) {
    if(error.status===401) {
      sessionPromise=null;
      // Refresh only read requests; never replay a potentially committed mutation.
      if(!options._sessionRefreshed&&(!options.method||options.method==='GET'))return finance(path,{...options,_sessionRefreshed:true});
    }
    throw error;
  }
}
