import {
  cloneLocatorCandidate,
  cloneLocatorDescriptor,
  type LocatorCandidate,
  type LocatorDescriptor,
} from '../action-model.js';

export function buildSelectorRecord(
  locator: LocatorDescriptor,
  locatorAlternatives: LocatorCandidate[] = [],
): {
  locator: LocatorDescriptor;
  locatorAlternatives: LocatorCandidate[];
} {
  return {
    locator: cloneLocatorDescriptor(locator),
    locatorAlternatives: locatorAlternatives.map(candidate => cloneLocatorCandidate(candidate)),
  };
}
