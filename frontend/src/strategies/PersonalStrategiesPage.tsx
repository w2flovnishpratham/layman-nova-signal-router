import { useEffect, useState } from 'react'
import type { AuthUser } from '../api'
import { getC2Config } from '../api'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AdminPineConversionWorkspace } from './AdminPineConversion'
import { C2MyStrategies } from './C2MyStrategies'
import { EngineStrategyPicker } from './EngineStrategyPicker'
import './personalStrategies.css'

export function PersonalStrategiesPage({ user }: { user?: AuthUser; focusInstanceId?: string | null }) {
  const [tab, setTab] = useState('engine')
  const [c2Enabled, setC2Enabled] = useState(false)

  useEffect(() => {
    let active = true
    getC2Config()
      .then(({ enabled }) => { if (active) setC2Enabled(enabled) })
      .catch(() => undefined)
    return () => { active = false }
  }, [])

  return (
    <div className="ps-page">
      <Tabs value={tab} onValueChange={setTab} className="ps-tabs">
        <TabsList variant="line" aria-label="Strategies">
          <TabsTrigger value="engine">Available strategies</TabsTrigger>
          {c2Enabled ? <TabsTrigger value="mine">My strategies</TabsTrigger> : null}
          {user?.is_admin ? <TabsTrigger value="admin">Admin strategies</TabsTrigger> : null}
        </TabsList>
        <TabsContent value="engine" className="ps-tab-panel">
          <EngineStrategyPicker />
        </TabsContent>
        {c2Enabled ? (
          <TabsContent value="mine" className="ps-tab-panel">
            <C2MyStrategies />
          </TabsContent>
        ) : null}
        {user?.is_admin ? (
          <TabsContent value="admin" className="ps-tab-panel">
            <AdminPineConversionWorkspace />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  )
}
