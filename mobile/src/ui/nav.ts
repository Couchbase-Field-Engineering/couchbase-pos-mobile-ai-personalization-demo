import { router } from 'expo-router';
import type { SuggestedAction } from '../types';
import type { IconName } from './components';

export const TOOL_LABEL: Record<string, string> = {
  place_order: 'Order',
  request_price_match: 'Price match',
  set_subscription: 'Subscribe & Save',
  create_return: 'Return',
  update_preferences: 'Preferences',
  issue_goodwill_credit: 'Goodwill credit',
  // Rung up in a store (drive-thru lane, phone line, self-checkout); arrives here on the shopper's channel.
  place_pos_order: 'In store',
};

export const TOOL_ICON: Record<string, IconName> = {
  place_order: 'bag-handle',
  request_price_match: 'pricetags',
  set_subscription: 'repeat',
  create_return: 'return-down-back',
  update_preferences: 'options',
  issue_goodwill_credit: 'gift',
  place_pos_order: 'storefront',
};

const SCREENS: Record<string, string> = {
  home: '/(tabs)', shop: '/(tabs)/shop', cart: '/(tabs)/cart', assistant: '/(tabs)/assistant', orders: '/(tabs)/orders',
  product: '/product', preferences: '/preferences', profile: '/profile',
};

/** Open the screen behind an assistant suggestion or a card CTA. */
export function openAction(a: SuggestedAction, title?: string) {
  if (a.screen) {
    const pathname = SCREENS[a.screen] ?? `/${a.screen}`;
    router.push({ pathname: pathname as never, params: Object.fromEntries(Object.entries(a.args ?? {}).map(([k, v]) => [k, String(v)])) });
    return;
  }
  if (a.tool) {
    router.push({
      pathname: '/confirm',
      params: { tool: a.tool, args: JSON.stringify(a.args ?? {}), title: title ?? a.label },
    });
  }
}
