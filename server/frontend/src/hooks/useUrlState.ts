import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { resolveOption, resolveStep, withSearchParam } from "@/app/routes";

/**
 * URL 선택값(탭·항목). 미지정은 기본값, 잘못된 값은 기본값으로 replace 정규화(기타 쿼리·state 보존).
 * hrefFor는 현재 쿼리를 보존한 이동 주소를 돌려준다(push 이동 → 뒤로·앞으로 가기 일치).
 */
export const useUrlOption = <T extends string>(name: string, allowed: readonly T[]) => {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const { value, invalid } = resolveOption(params.get(name), allowed);
  useEffect(() => {
    if (invalid)
      navigate(
        { pathname: location.pathname, search: withSearchParam(location.search, name, value), hash: location.hash },
        { replace: true, state: location.state },
      );
  }, [invalid, location, name, navigate, value]);
  const hrefFor = (next: T) => location.pathname + withSearchParam(location.search, name, next);
  return { value, hrefFor };
};

/** 작업 영역 단계(step=1..max). 단계 이동은 Link(push)라 뒤로·앞으로 가기가 단계와 일치한다. */
export const useUrlStep = (max: number, name = "step") => {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const { value, invalid } = resolveStep(params.get(name), max);
  useEffect(() => {
    if (invalid)
      navigate(
        {
          pathname: location.pathname,
          search: withSearchParam(location.search, name, String(value)),
          hash: location.hash,
        },
        { replace: true, state: location.state },
      );
  }, [invalid, location, name, navigate, value]);
  const hrefFor = (step: number) => location.pathname + withSearchParam(location.search, name, String(step));
  return { value, hrefFor };
};
