import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import Layout from './components/Layout'
import ControlsPage from './pages/ControlsPage'
import DashboardPage from './pages/DashboardPage'
import LandingPage from './pages/LandingPage'
import LogsPage from './pages/LogsPage'
import OrdersPage from './pages/OrdersPage'
import PositionsPage from './pages/PositionsPage'
import SettingsPage from './pages/SettingsPage'
import SetupPage from './pages/SetupPage'

export default function App() {
  return (
    <BrowserRouter>
      {/* Global toasts — bottom-right, dark theme */}
      <Toaster
        position="bottom-right"
        theme="dark"
        toastOptions={{
          style: {
            background: '#151513',
            border: '1px solid #2b2a26',
            color: '#f4f1ea',
            fontSize: '13px',
          },
        }}
      />

      <Routes>
        {/* Landing page — no sidebar */}
        <Route path="/" element={<LandingPage />} />

        {/* App shell with sidebar + topbar */}
        <Route path="/app" element={<Layout />}>
          <Route index element={<Navigate to="/app/dashboard" replace />} />
          <Route path="dashboard"  element={<DashboardPage />}  />
          <Route path="setup"      element={<SetupPage />}       />
          <Route path="orders"     element={<OrdersPage />}      />
          <Route path="positions"  element={<PositionsPage />}   />
          <Route path="logs"       element={<LogsPage />}        />
          <Route path="controls"   element={<ControlsPage />}    />
          <Route path="settings"   element={<SettingsPage />}    />
        </Route>

        {/* Legacy short paths — redirect into /app */}
        <Route path="/dashboard"  element={<Navigate to="/app/dashboard"  replace />} />
        <Route path="/setup"      element={<Navigate to="/app/setup"      replace />} />
        <Route path="/orders"     element={<Navigate to="/app/orders"     replace />} />
        <Route path="/positions"  element={<Navigate to="/app/positions"  replace />} />
        <Route path="/logs"       element={<Navigate to="/app/logs"       replace />} />
        <Route path="/controls"   element={<Navigate to="/app/controls"   replace />} />
        <Route path="/settings"   element={<Navigate to="/app/settings"   replace />} />
        <Route path="*"           element={<Navigate to="/"               replace />} />
      </Routes>
    </BrowserRouter>
  )
}
