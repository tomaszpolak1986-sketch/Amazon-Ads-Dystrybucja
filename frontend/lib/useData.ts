"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

interface State<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Cienka nakladka na TanStack Query zachowujaca proste API {data, loading, error, reload}.
 *
 * PIERWSZY argument to klucz cache i MUSI zaczynac sie od nazwy zasobu.
 * Wczesniej klucz powstawal tylko z zaleznosci, wiec dwa rozne zapytania
 * o tych samych parametrach (np. lista regul i slownik metadanych, oba bez
 * parametrow) trafialy na ten sam wpis w cache i drugie dostawalo dane
 * pierwszego. Nazwa zasobu w kluczu eliminuje ta kolizje.
 *
 * `placeholderData` sprawia, ze przy zmianie zakresu dat tabela pokazuje
 * poprzednie dane zamiast migac pustka.
 */
export function useData<T>(key: readonly unknown[], fetcher: () => Promise<T>): State<T> {
  const queryClient = useQueryClient();
  const queryKey = useMemo(() => ["adspanel", ...key], [key]);

  const query = useQuery({
    queryKey,
    queryFn: fetcher,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: 1,
  });

  const reload = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey });
  }, [queryClient, queryKey]);

  return {
    data: query.data ?? null,
    loading: query.isPending || query.isFetching,
    error: query.error ? (query.error as Error).message : null,
    reload,
  };
}
