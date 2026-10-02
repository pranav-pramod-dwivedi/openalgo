import { useEffect, useState } from 'react'
import type { Socket } from 'socket.io-client'

export function useSocketOnline(socket: Socket | null): boolean {
  const [online, setOnline] = useState(() => socket?.connected ?? false)

  useEffect(() => {
    if (!socket) {
      setOnline(false)
      return
    }
    const update = () => setOnline(socket.connected)
    setOnline(socket.connected)
    socket.on('connect', update)
    socket.on('disconnect', update)
    return () => {
      socket.off('connect', update)
      socket.off('disconnect', update)
    }
  }, [socket])

  return online
}
