import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { SessionProvider } from '../src/app/session'
import { InterpreterSettings } from '../src/components/InterpreterSettings'
afterEach(()=>vi.unstubAllGlobals())
const state={model:'gpt-6.1-sol',revision:1,can_manage:true,models:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol'},{id:'gpt-6-luna',label:'GPT-6 Luna'}]}
it('loads on demand and saves only model plus revision without replaying analyses',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>state}).mockResolvedValueOnce({ok:true,json:async()=>({...state,model:'gpt-6-luna',revision:2})});vi.stubGlobal('fetch',fetcher)
 render(<SessionProvider><InterpreterSettings/></SessionProvider>);expect(fetcher).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'模型设置'}))
 const select=await screen.findByLabelText('用于对话提炼的模型');fireEvent.change(select,{target:{value:'gpt-6-luna'}});fireEvent.click(screen.getByRole('button',{name:'保存模型'}))
 await screen.findByText('已保存，之后开始的分析将使用此模型。')
 const saved=fetcher.mock.calls[1]!
 expect(saved[0]).toBe('/_local/interpreter');expect(JSON.parse(saved[1].body)).toEqual({model:'gpt-6-luna',expected_revision:1});expect(fetcher).toHaveBeenCalledTimes(2)
})
it('keeps readers read-only and explains external processing',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({...state,can_manage:false})}));render(<SessionProvider><InterpreterSettings/></SessionProvider>);fireEvent.click(screen.getByRole('button',{name:'模型设置'}))
 const select=await screen.findByLabelText('用于对话提炼的模型');expect((select as HTMLSelectElement).disabled).toBe(true);expect((screen.getByRole('button',{name:'保存模型'}) as HTMLButtonElement).disabled).toBe(true)
 expect(screen.getByText(/待提炼消息会发送给所选 OpenAI 模型/)).toBeTruthy();expect(screen.getByText('当前凭证只可查看模型设置。')).toBeTruthy()
})
it('does not claim success after a concurrent operator change',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>state}).mockResolvedValueOnce({ok:false,status:409});vi.stubGlobal('fetch',fetcher);render(<SessionProvider><InterpreterSettings/></SessionProvider>);fireEvent.click(screen.getByRole('button',{name:'模型设置'}));fireEvent.change(await screen.findByLabelText('用于对话提炼的模型'),{target:{value:'gpt-6-luna'}});fireEvent.click(screen.getByRole('button',{name:'保存模型'}))
 await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('设置已在另一处更新'));expect(screen.queryByText(/已保存，/)).toBeNull()
})
