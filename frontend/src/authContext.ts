import { createContext, useContext } from "react";

export interface AuthContextValue {
  /** Whether the server requires a token at all. */
  enabled: boolean;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  enabled: false,
  logout: () => {},
});

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
