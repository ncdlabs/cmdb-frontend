import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { BrowsePage } from './BrowsePage'
import { SettingsPage } from './SettingsPage'
import { StyleGuidePage } from './StyleGuidePage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<BrowsePage />} />
        <Route path="/ci/:itemId" element={<BrowsePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/style-guide" element={<StyleGuidePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
