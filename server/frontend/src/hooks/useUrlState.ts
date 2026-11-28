import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { resolveOption, resolveStep, withSearchParam } from "@/app/routes";

/**
 * 이동 주소에 붙일 화면 영역 해시(선택). 같은 화면 안 작업 영역(새 학습·새 비교 평가)의 단계·틀 이동이 영역 해시를 유지해야
 * 화면 이동 스크롤(useDetectionScroll)이 "해시만 지운 이동"으로 보고 맨 위로 올리지 않는다. 넘기지 않으면 지금처럼 해시 없음.
 */
type UrlStateOptions = { hash?: string };
const withHash = (href: string, hash: string | undefined) => (hash ? `${href}#${hash}` : href);

/**
 * URL 선택값(탭·항목). 미지정은 기본값, 잘못된 값은 기본값으로 replace 정규화(기타 쿼리·state 보존).
 * hrefFor는 현재 쿼리를 보존한 이동 주소를 돌려준다(push 이동 → 뒤로·앞으로 가기 일치). `hash`를 주면 그 영역 해시를 붙인다.
 */
export const useUrlOption = <T extends string>(name: string, allowed: readonly T[], { hash }: UrlStateOptions = {}) => {
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
  const hrefFor = (next: T) => withHash(location.pathname + withSearchParam(location.search, name, next), hash);
  return { value, hrefFor };
};

/** 작업 영역 단계(step=1..max). 단계 이동은 Link(push)라 뒤로·앞으로 가기가 단계와 일치한다. */
export const useUrlStep = (max: number, name = "step", { hash }: UrlStateOptions = {}) => {
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
  const hrefFor = (step: number) => withHash(location.pathname + withSearchParam(location.search, name, String(step)), hash);
  return { value, hrefFor };
};
