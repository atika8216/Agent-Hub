import { useCurrentUser as useCurrentUserApi } from "@/lib/api";

export function useCurrentUser() {
  const query = useCurrentUserApi({
    query: {
      staleTime: Infinity,
    },
  });
  const user = query.data?.data;
  return {
    ...query,
    user,
    isAdmin: user?.role === "admin",
  };
}
