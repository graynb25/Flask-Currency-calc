# Python Currency Calc


# 1. Get user inputs and ensure uppercase consistency
currency = input("Choose your currency to exchange (USD, COP, EUR): ").upper()
amount = float(input("Enter the amount to convert: "))
locurrency = input("Enter your local currency (USD, COP, EUR): ").upper()
exrate = float(input("Enter the exchange rate (1 unit of source = X units of local): "))

# 2. Check if currencies are identical
if currency == locurrency:
    result = amount
    print(f"No conversion needed. {amount:.2f} {currency} is equal to {result:.2f} {locurrency}")

# 3. Handle different conversion combinations
elif currency == "USD" and locurrency == "EUR":
    result = amount * exrate
    print(f"{amount:.2f} {currency} converted to {locurrency} is {result:.2f}")

elif currency == "EUR" and locurrency == "USD":
    result = amount * exrate
    print(f"{amount:.2f} {currency} converted to {locurrency} is {result:.2f}")

elif currency == "USD" and locurrency == "COP":
    result = amount * exrate
    print(f"{amount:.2f} {currency} converted to {locurrency} is {result:.2f}")

elif currency == "COP" and locurrency == "USD":
    result = amount * exrate
    print(f"{amount:.2f} {currency} converted to {locurrency} is {result:.2f}")

elif currency == "EUR" and locurrency == "COP":
    result = amount * exrate
    print(f"{amount:.2f} {currency} converted to {locurrency} is {result:.2f}")

elif currency == "COP" and locurrency == "EUR":
    result = amount * exrate
    print(f"{amount:.2f} {currency} converted to {locurrency} is {result:.2f}")

# 4. Catch unsupported currency inputs
else:
    print("Error: One or both of the currencies entered are not supported.")








