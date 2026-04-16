#include <stdio.h>

int main() {
    int value1, value2, sum;

    printf("Enter first value: ");
    scanf("%d", &value1);

    printf("Enter second value: ");
    scanf("%d", &value2);

    sum = value1 + value2;

    printf("You entered: %d and %d\n", value1, value2);
    printf("Sum: %d\n", sum);

    return 0;
}
